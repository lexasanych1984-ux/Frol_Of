Attribute VB_Name = "Futerovka"
'==============================================================================
' Futerovka v19 - футеровка отдельной деталью, рабочий слой - САРКОФАГ
'------------------------------------------------------------------------------
' v19 = v18 + два исправления по прогону v18:
'   1. ЦВЕТА: Combine ("Соединить") и "Полость" сбрасывали цвета тел -
'      всё становилось серым. Теперь имена и цвета назначаются заново
'      В САМОМ КОНЦЕ (после полости), всем телам каждого слоя (включая
'      куски, на которые тело разрезали рёбра).
'   2. ТОРЦЫ ИЗОЛЯЦИИ: добавлены диалоги "отметка ВЕРХА футеровки" и
'      "отметка НИЗА футеровки" (пусто = не закрывать). Изоляция и её
'      клон срезаются на 150 мм (OVERLAP_MM) от торца ДО построения
'      рабочего слоя - рабочий слой сам заполняет полосу до кожуха и
'      закрывает торец изоляции. Направление среза задают константы
'      CUT_FLIP_TOP/CUT_FLIP_BOTTOM (если срежет не ту сторону -
'      поменять True/False).
' v18: РАБОЧИЙ СЛОЙ ОБВОЛАКИВАЕТ ИЗОЛЯЦИЮ (нет открытых участков):
'   - изоляция: эквидистанта 0 + утолщение на толщину изоляции;
'   - временный КЛОН изоляции: ещё одна эквидистанта 0 + такое же
'     утолщение (нужен потому, что Combine-Subtract расходует
'     вычитаемые тела - настоящую изоляцию трогать нельзя);
'   - рабочий слой: эквидистанта 0 + утолщение НА ПОЛНУЮ ТОЛЩИНУ
'     (изоляция + рабочий) прямо от кожуха;
'   - из рабочего слоя вычитается клон (InsertCombineFeature 15902:
'     основное тело метка 1, инструменты метка 2 - записано рекордером);
'     результат: рабочий слой закрывает изоляцию везде, где она
'     кончается (рёбра, ложе, разрывы граней).
'   Продление листов из v17 УДАЛЕНО (тупик: продление не может
'   заполнить треугольник у кожуха - утолщение идёт внутрь, а не к
'   кожуху). Клинья закрываются выделением граней ложа при запуске.
' v16 = v15 БЕЗ обрезки верха: по решению пользователя верх обрезается
'   вручную в режиме правки (фаза 4 из v14/v15 удалена вместе с диалогом
'   отметки; в прогоне v15 она всё равно не выделяла тела).
' v15 = v14 + исправлена ошибка компиляции: переменная iS конфликтовала
'   с ключевым словом VBA "Is" (как раньше iF с "If") - переименована
'   в idxS. Правило: НЕ называть переменные iF, iS, iN и т.п.
'------------------------------------------------------------------------------
' ЕДИНСТВЕННЫЙ НУЖНЫЙ МОДУЛЬ. Старые модули из проекта удалить
' (в т.ч. заглушку futerovka1, оставшуюся от записи макроса).
'
' v14 = v13 + ФАЗА 4 "Обрезка верха": по введённой отметке (мм от начала
'   координат сборки = низ горизонтальной плоскости обечайки) строится
'   плоскость и всё выше неё срезается по всем телам футеровки.
'   Пустой ввод/Отмена в диалоге отметки = не обрезать.
'   Вызовы записаны рекордером 05.07.2026:
'     SelectByID2("Сверху", "PLANE", 0,0,0, True, 0, Nothing, 0)
'     FeatureManager.InsertRefPlane 8, <метры>, 0, 0, 0, 0
'     SelectByID2(<плоскость>, "PLANE", ...) + тела SOLIDBODY (append)
'     ModelDoc.InsertCutSurface True, 0
'   Тела после "Полости" переименованы в "Полость1[n]" (имена нестабильны),
'   поэтому тела для обрезки выделяются ОБЪЕКТАМИ из GetBodies2.
' v13 = v12 + ФАЗА 3 "Полость": из тел футеровки вычитается компонент
'   кожуха ЦЕЛИКОМ (вместе с рёбрами жёсткости) - футеровка прерывается
'   на рёбрах и примыкает к ним снизу и сверху. Вызов записан рекордером:
'     SelectByID2("<компонент>@<сборка>", "COMPONENT", 0,0,0, True, 0, ...)
'     ModelDoc(сборки).InsertCavity4 0, 0, 0, True, 1, -1
'   (в режиме редактирования детали в контексте). Компонент кожуха
'   определяется автоматически по выделенным граням.
' v12 = v11 + слои НЕ объединяются в одно тело. Найдено по прогону v11:
'   5-й аргумент FeatureBossThicken - это галочка "Объединить результаты";
'   с True (как в записи рекордера) рабочий слой сливался с изоляцией в
'   одно тело и перекрашивал его - в дереве было "Твердые тела(2)" вместо
'   четырёх, изоляция "исчезала". Теперь 5-й аргумент = False: каждый
'   лист каждого слоя - отдельное тело со своим цветом.
' v11 = v10 + толщины слоёв запрашиваются ДИАЛОГАМИ при запуске:
'   - изоляция (по умолчанию 65 мм) и рабочий слой (по умолчанию 250 мм);
'   - 0 = этот слой не строить (можно футеровать одним слоем);
'   - "Отмена" = выход из макроса;
'   - имена тел/элементов включают введённую толщину (Izolyaciya_80 и т.п.).
' v10 = v9 + перебор ЧЕТЫРЁХ способов выделения тела + диагностика.
'   Итог прогона v9: фаза 1 работает целиком (деталь, 2 эквидистанты,
'   4 тела поверхности, окно детали активировалось), но SelectByID2
'   с Mark=1 в свежесозданной детали вернул False - хотя тот же вызов,
'   записанный рекордером в открытой детали, работал. Видимо, в только
'   что созданной несохранённой детали тело ещё не ищется ПО ИМЕНИ.
'   v10 дополнительно выделяет тело ОБЪЕКТОМ (минуя имя): Body2.Select2,
'   Extension.MultiSelect2, SelectionMgr.AddSelectionListObject - все
'   с меткой Mark=1. При полном отказе показывает диагностику: активный
'   документ, имена всех тел детали, результат каждого способа.
' v9 = v8 + точные вызовы из ЗАПИСАННОГО пользователем макроса:
'   - выделение тела: SelectByID2(имя, "SURFACEBODY", 0,0,0, False, 1, ...)
'     с меткой Mark=1 - именно так выделяет SolidWorks перед командой
'     "Придать толщину" (раньше стояла метка 0 - вероятная причина отказов);
'   - сигнатура FeatureBossThicken ПОДТВЕРЖДЕНА записью:
'     FeatureBossThicken(толщина, 0, 0, False, True, True, True).
' v8 = v7 + ForceRebuild3 детали после активации её окна (элементы с
'     внешними ссылками до перестройки не дают выделить свои тела).
' v7 = v6 + исправлена переменная цикла (iF конфликтовала с ключевым словом If):
' v6:
'   - каждый слой может состоять из НЕСКОЛЬКИХ листов поверхности
'     (если выделенные грани кожуха не смежные) - утолщается каждый лист;
'   - выделение тел по ИМЕНИ через SelectByID2/"SURFACEBODY" - так же,
'     как это делает сам SolidWorks при записи макросов; старый способ
'     оставлен как запасной;
'   - имена тел собираются заранее, до утолщений (имена переживают
'     перестройки модели, указатели - нет);
'   - активация окна детали без принудительной перестройки.
'
' Порядок работы:
'   1. Откройте сборку, удалите старые Futerovka из дерева.
'   2. Параметры > Внешние ссылки: "Не создавать ссылки, внешние по
'      отношению к модели" - флажок СНЯТ.
'   3. Выделите Ctrl+кликом ВНУТРЕННИЕ грани кожуха.
'   4. Запустите макрос.
'   5. Потом: правой кнопкой по Futerovka > "Сохранить деталь
'      (во внешнем файле)".
'==============================================================================
Option Explicit

Const MM As Double = 0.001
Dim gStep As String

Sub main()

    '=================== ПАРАМЕТРЫ СЛОЁВ ======================================
    ' Толщины запрашиваются диалогами при запуске (см. ниже AskThickness).
    ' Внутренних "слоёв" до трёх: изоляция, её временный клон (инструмент
    ' для саркофага) и рабочий слой полной толщины.
    Const MAX_LAYERS As Long = 3

    Dim thk(1 To MAX_LAYERS) As Double       ' толщина утолщения, мм
    Dim offMM(1 To MAX_LAYERS) As Double     ' смещение эквидистанты, мм
    Dim nam(1 To MAX_LAYERS) As String
    Dim col(1 To MAX_LAYERS) As Long
    Dim numLayers As Long
    Dim cloneIdx As Long                     ' индекс клона (0 - нет)
    Dim rabIdx As Long                       ' индекс рабочего слоя (0 - нет)

    Const DEF_IZOL As Double = 65            ' значения по умолчанию, мм
    Const DEF_RAB As Double = 250

    ' Заход рабочего слоя на торцы изоляции (саркофаг), мм
    Const OVERLAP_MM As Double = 150
    ' Направление среза изоляции плоскостью. Если после запуска
    ' срезана НЕ ТА сторона - поменять True/False местами.
    Const CUT_FLIP_TOP As Boolean = True     ' срез ВЫШЕ плоскости
    Const CUT_FLIP_BOTTOM As Boolean = False ' срез НИЖЕ плоскости

    Const COMP_NAME As String = "Futerovka"
    Const THICKEN_DIR As Long = 0            ' предпочтительная сторона (0/2)
    Const OFFSET_REVERSE As Boolean = False
    '==========================================================================

    Dim swApp As SldWorks.SldWorks
    Dim swAssyModel As SldWorks.ModelDoc2
    Dim swAssy As SldWorks.AssemblyDoc
    Dim swSelMgr As SldWorks.SelectionMgr

    gStep = "подключение к SolidWorks"
    Set swApp = Application.SldWorks
    Set swAssyModel = swApp.ActiveDoc

    If swAssyModel Is Nothing Then
        MsgBox "Откройте сборку.", vbExclamation
        Exit Sub
    End If
    If swAssyModel.GetType <> swDocASSEMBLY Then
        MsgBox "Макрос запускается в СБОРКЕ: откройте сборку, выделите " & _
               "внутренние грани кожуха и запустите снова.", vbExclamation
        Exit Sub
    End If

    Set swAssy = swAssyModel
    Set swSelMgr = swAssyModel.SelectionManager

    Dim assyTitle As String
    assyTitle = swAssyModel.GetTitle

    '---------------- Выделенные грани кожуха ---------------------------------
    gStep = "чтение выделенных граней"
    Dim nSel As Long
    nSel = swSelMgr.GetSelectedObjectCount2(-1)
    If nSel = 0 Then
        MsgBox "Сначала выделите внутренние грани кожуха (Ctrl+клик).", _
               vbExclamation
        Exit Sub
    End If

    Dim faces() As SldWorks.Face2
    ReDim faces(1 To nSel)
    Dim i As Long
    For i = 1 To nSel
        If swSelMgr.GetSelectedObjectType3(i, -1) <> swSelFACES Then
            MsgBox "В выборке есть не-грани (элемент " & i & "). " & _
                   "Выделите только грани кожуха.", vbExclamation
            Exit Sub
        End If
        Set faces(i) = swSelMgr.GetSelectedObject6(i, -1)
    Next i

    ' Компонент кожуха - по первой выделенной грани (нужен для фазы 3)
    gStep = "определение компонента кожуха"
    Dim swShellComp As SldWorks.Component2
    On Error Resume Next
    Set swShellComp = swSelMgr.GetSelectedObjectsComponent4(1, -1)
    On Error GoTo 0

    '---------------- Толщины слоёв (диалоги) ---------------------------------
    gStep = "ввод толщин слоёв"
    Dim thkIz As Double, thkRab As Double

    thkIz = AskThickness("ИЗОЛЯЦИОННОГО (первый от кожуха)", DEF_IZOL)
    If thkIz < 0 Then Exit Sub                    ' отмена
    thkRab = AskThickness("РАБОЧЕГО (внутренний)", DEF_RAB)
    If thkRab < 0 Then Exit Sub                   ' отмена

    If thkIz = 0 And thkRab = 0 Then
        MsgBox "Обе толщины нулевые - строить нечего.", vbExclamation
        Exit Sub
    End If

    numLayers = 0
    cloneIdx = 0
    rabIdx = 0
    If thkIz > 0 Then
        numLayers = numLayers + 1
        thk(numLayers) = thkIz
        offMM(numLayers) = 0
        nam(numLayers) = "Izolyaciya_" & NiceMM(thkIz)
        col(numLayers) = RGB(255, 220, 100)
    End If
    If thkIz > 0 And thkRab > 0 Then
        ' временный клон изоляции - инструмент для саркофага
        numLayers = numLayers + 1
        thk(numLayers) = thkIz
        offMM(numLayers) = 0
        nam(numLayers) = "Tmp_klon_izolyacii"
        col(numLayers) = RGB(128, 128, 128)
        cloneIdx = numLayers
    End If
    If thkRab > 0 Then
        numLayers = numLayers + 1
        offMM(numLayers) = 0
        If thkIz > 0 Then
            thk(numLayers) = thkIz + thkRab   ' саркофаг: полная толщина
        Else
            thk(numLayers) = thkRab
        End If
        nam(numLayers) = "Rabochiy_sloy_" & NiceMM(thkRab)
        col(numLayers) = RGB(200, 90, 60)
        rabIdx = numLayers
    End If

    '------- Отметки торцов: заход рабочего слоя на изоляцию (150 мм) --------
    Dim capTop As Boolean, capBot As Boolean
    Dim capTopMM As Double, capBotMM As Double
    Dim sCap As String

    capTop = False: capBot = False
    If cloneIdx > 0 Then      ' смысл есть только при двух слоях
        gStep = "ввод отметки верха футеровки"
        sCap = InputBox("Отметка ВЕРХА футеровки, мм" & vbCrLf & _
                        "(по вертикали от начала координат сборки; " & _
                        "можно отрицательную)." & vbCrLf & _
                        "Изоляция будет срезана на " & OVERLAP_MM & _
                        " мм НИЖЕ, рабочий слой закроет её торец." & _
                        vbCrLf & vbCrLf & _
                        "Пусто или Отмена - торец сверху не закрывать.", _
                        "Футеровка: торец изоляции сверху", "")
        If StrPtr(sCap) <> 0 Then
            sCap = Trim$(Replace(sCap, ",", "."))
            If sCap <> "" Then
                capTopMM = Val(sCap)
                If capTopMM = 0 And Left$(sCap, 1) <> "0" And _
                   Left$(sCap, 2) <> "-0" Then
                    MsgBox "Не удалось понять отметку: '" & sCap & "'.", _
                           vbExclamation
                    Exit Sub
                End If
                capTop = True
            End If
        End If

        gStep = "ввод отметки низа футеровки"
        sCap = InputBox("Отметка НИЗА футеровки, мм" & vbCrLf & _
                        "(по вертикали от начала координат сборки; " & _
                        "можно отрицательную)." & vbCrLf & _
                        "Изоляция будет срезана на " & OVERLAP_MM & _
                        " мм ВЫШЕ, рабочий слой закроет её торец." & _
                        vbCrLf & vbCrLf & _
                        "Пусто или Отмена - торец снизу не закрывать.", _
                        "Футеровка: торец изоляции снизу", "")
        If StrPtr(sCap) <> 0 Then
            sCap = Trim$(Replace(sCap, ",", "."))
            If sCap <> "" Then
                capBotMM = Val(sCap)
                If capBotMM = 0 And Left$(sCap, 1) <> "0" And _
                   Left$(sCap, 2) <> "-0" Then
                    MsgBox "Не удалось понять отметку: '" & sCap & "'.", _
                           vbExclamation
                    Exit Sub
                End If
                capBot = True
            End If
        End If
    End If

    On Error GoTo FAIL

    '==========================================================================
    ' ФАЗА 1: сборка - деталь + эквидистантные поверхности
    '==========================================================================
    gStep = "создание новой детали (InsertNewVirtualPart)"
    Dim swComp As SldWorks.Component2
    Dim status As Long

    swAssyModel.ClearSelection2 True
    status = swAssy.InsertNewVirtualPart(Nothing, swComp)
    If status < 0 Or swComp Is Nothing Then
        MsgBox "Не удалось создать деталь в сборке (код " & status & ").", _
               vbCritical
        Exit Sub
    End If

    On Error Resume Next
    swComp.Name2 = COMP_NAME
    On Error GoTo FAIL

    gStep = "получение документа новой детали"
    Dim swPartDoc As SldWorks.ModelDoc2
    Set swPartDoc = swComp.GetModelDoc2
    If swPartDoc Is Nothing Then
        MsgBox "Документ новой детали не загрузился. Сохраните сборку и " & _
               "повторите.", vbCritical
        Exit Sub
    End If

    gStep = "вход в режим редактирования детали"
    swAssyModel.ClearSelection2 True
    Dim ok As Boolean
    ok = swComp.Select4(False, Nothing, False)
    Dim nInfo As Long
    swAssy.EditPart2 True, True, nInfo

    Dim offFeat(1 To MAX_LAYERS) As SldWorks.Feature

    Dim iLayer As Long
    Dim swEnt As SldWorks.Entity
    Dim cntBefore As Long

    For iLayer = 1 To numLayers
        gStep = "слой " & iLayer & ": выделение граней кожуха"
        swAssyModel.ClearSelection2 True
        For i = 1 To nSel
            Set swEnt = faces(i)
            ok = swEnt.Select4(True, Nothing)
        Next i

        gStep = "слой " & iLayer & ": эквидистанта, смещение " & _
                offMM(iLayer) & " мм"
        cntBefore = swPartDoc.GetFeatureCount
        swAssyModel.InsertOffsetSurface offMM(iLayer) * MM, OFFSET_REVERSE

        If swPartDoc.GetFeatureCount <= cntBefore Then
            MsgBox "Эквидистанта слоя " & iLayer & " не построилась " & _
                   "(смещение " & offMM(iLayer) & " мм).", vbCritical
            GoTo CLEANUP_ASSY
        End If

        Set offFeat(iLayer) = swPartDoc.FeatureByPositionReverse(0)
        offFeat(iLayer).Name = "Offset_" & nam(iLayer)
    Next iLayer

    gStep = "выход в режим сборки"
    swAssyModel.ClearSelection2 True
    swAssy.EditAssembly

    '==========================================================================
    ' ФАЗА 2: окно детали - утолщение каждого листа каждого слоя
    '==========================================================================
    gStep = "активация окна детали"
    Dim nErr As Long
    swApp.ActivateDoc3 swPartDoc.GetTitle, False, _
                       swRebuildOnActivation_e.swDontRebuildActiveDoc, nErr

    ' Контроль: активным должно стать именно окно детали
    Dim swActDoc As SldWorks.ModelDoc2
    Set swActDoc = swApp.ActiveDoc
    If swActDoc Is Nothing Then
        swApp.ActivateDoc3 swPartDoc.GetPathName, False, _
                           swRebuildOnActivation_e.swDontRebuildActiveDoc, nErr
    ElseIf swActDoc.GetTitle <> swPartDoc.GetTitle Then
        swApp.ActivateDoc3 swPartDoc.GetPathName, False, _
                           swRebuildOnActivation_e.swDontRebuildActiveDoc, nErr
    End If

    ' Обязательная перестройка: тела элементов с внешними ссылками (->)
    ' до пересчёта находятся в состоянии "не перестроен" и не выделяются.
    gStep = "перестройка детали"
    swPartDoc.ForceRebuild3 True
    swPartDoc.ForceRebuild3 False

    ' --- 2а. собираем ИМЕНА тел каждого слоя
    gStep = "сбор имён тел поверхности по слоям"
    Dim bodyNames() As String     ' bodyNames(iLayer, k)
    Dim bodyCount(1 To MAX_LAYERS) As Long
    Dim thkFeat(1 To MAX_LAYERS, 1 To 32) As SldWorks.Feature
    ReDim bodyNames(1 To MAX_LAYERS, 1 To 32)

    For iLayer = 1 To numLayers
        bodyCount(iLayer) = CollectBodyNames(offFeat(iLayer), _
                                             bodyNames, iLayer)
        If bodyCount(iLayer) = 0 Then
            MsgBox "У слоя " & iLayer & " не найдено тел поверхности.", _
                   vbCritical
            GoTo CLEANUP_PART
        End If
    Next iLayer

    ' --- 2б. утолщаем каждый лист
    Dim swThk As SldWorks.Feature
    Dim k As Long
    Dim builtSheets As Long, totalSheets As Long
    builtSheets = 0: totalSheets = 0
    For iLayer = 1 To numLayers
        totalSheets = totalSheets + bodyCount(iLayer)
    Next iLayer

    For iLayer = 1 To numLayers
        For k = 1 To bodyCount(iLayer)

            gStep = "слой " & iLayer & ", лист " & k & _
                    " ('" & bodyNames(iLayer, k) & "'): выделение"
            Dim selDiag As String
            Dim actTitle As String
            If Not SelectSurfaceBody(swPartDoc, bodyNames(iLayer, k), _
                                     selDiag) Then
                actTitle = "?"
                On Error Resume Next
                actTitle = swApp.ActiveDoc.GetTitle
                On Error GoTo FAIL
                MsgBox "Не выделилось тело '" & bodyNames(iLayer, k) & _
                       "' (слой " & iLayer & ", лист " & k & ")." & _
                       vbCrLf & vbCrLf & _
                       "Активный документ: " & actTitle & vbCrLf & _
                       "Диагностика:" & vbCrLf & selDiag, vbCritical
                GoTo CLEANUP_PART
            End If

            gStep = "слой " & iLayer & ", лист " & k & ": утолщение " & _
                    thk(iLayer) & " мм"
            Set swThk = TryThicken(swPartDoc.FeatureManager, _
                                   thk(iLayer) * MM, THICKEN_DIR)

            If swThk Is Nothing Then
                gStep = "слой " & iLayer & ", лист " & k & _
                        ": утолщение, обратная сторона"
                If SelectSurfaceBody(swPartDoc, bodyNames(iLayer, k), _
                                     selDiag) Then
                    Set swThk = TryThicken(swPartDoc.FeatureManager, _
                                           thk(iLayer) * MM, _
                                           IIf(THICKEN_DIR = 0, 2, 0))
                End If
            End If

            If swThk Is Nothing Then
                MsgBox "Утолщение не выполнено: слой " & iLayer & _
                       ", лист " & k & ".", vbCritical
                GoTo CLEANUP_PART
            End If

            swThk.Name = nam(iLayer) & "_" & k
            Set thkFeat(iLayer, k) = swThk

            gStep = "слой " & iLayer & ", лист " & k & ": имя и цвет"
            Dim swBody As SldWorks.Body2
            Set swBody = FirstBodyOfFeature(swThk)
            If Not swBody Is Nothing Then
                On Error Resume Next
                swBody.Name = nam(iLayer) & "_" & k
                On Error GoTo FAIL
                ColorBody swBody, col(iLayer)
            End If

            builtSheets = builtSheets + 1
        Next k

        '----------------------------------------------------------------------
        ' После изоляции и клона (ДО рабочего слоя!) - срез их торцов на
        ' OVERLAP_MM от отметок, чтобы рабочий слой закрыл торцы изоляции.
        ' Срезаются только уже построенные тела (изоляция + клон): даже
        ' если вырез заденет "все тела", рабочего слоя ещё нет.
        ' InsertRefPlane / InsertCutSurface - записаны рекордером.
        '----------------------------------------------------------------------
        If iLayer = cloneIdx And (capTop Or capBot) Then
            Dim capPass As Long
            Dim capElev As Double
            Dim capFlip As Boolean
            Dim capName As String
            Dim oCapPlane As Object
            Dim swCapPlaneF As SldWorks.Feature
            Dim nCapSel As Long
            Dim iCap As Long, kCap As Long
            Dim swCapB As SldWorks.Body2

            For capPass = 1 To 2
                If capPass = 1 And Not capTop Then GoTo NEXT_CAP
                If capPass = 2 And Not capBot Then GoTo NEXT_CAP
                If capPass = 1 Then
                    capElev = capTopMM - OVERLAP_MM
                    capFlip = CUT_FLIP_TOP
                    capName = "Srez_izolyacii_verh"
                Else
                    capElev = capBotMM + OVERLAP_MM
                    capFlip = CUT_FLIP_BOTTOM
                    capName = "Srez_izolyacii_niz"
                End If

                gStep = "срез изоляции (" & capName & ") на отметке " & _
                        capElev & " мм: плоскость"
                swPartDoc.ClearSelection2 True
                ok = swPartDoc.Extension.SelectByID2("Сверху", "PLANE", _
                         0, 0, 0, True, 0, Nothing, 0)
                Set oCapPlane = Nothing
                If ok Then
                    Set oCapPlane = swPartDoc.FeatureManager.InsertRefPlane( _
                                        8, capElev * MM, 0, 0, 0, 0)
                End If
                If oCapPlane Is Nothing Then
                    MsgBox "Плоскость среза изоляции на отметке " & _
                           capElev & " мм не создалась - этот торец " & _
                           "останется незакрытым.", vbExclamation
                    GoTo NEXT_CAP
                End If
                Set swCapPlaneF = swPartDoc.FeatureByPositionReverse(0)
                On Error Resume Next
                swCapPlaneF.Name = capName
                On Error GoTo FAIL

                gStep = "срез изоляции (" & capName & "): выделение"
                swPartDoc.ClearSelection2 True
                ok = swPartDoc.Extension.SelectByID2(swCapPlaneF.Name, _
                         "PLANE", 0, 0, 0, True, 0, Nothing, 0)
                nCapSel = 0
                If ok Then
                    For iCap = 1 To cloneIdx        ' изоляция и клон
                        For kCap = 1 To bodyCount(iCap)
                            Set swCapB = FirstBodyOfFeature( _
                                             thkFeat(iCap, kCap))
                            If Not swCapB Is Nothing Then
                                If SelectBodyObj(swPartDoc, swCapB, _
                                                 0, True) Then
                                    nCapSel = nCapSel + 1
                                End If
                            End If
                        Next kCap
                    Next iCap
                End If

                If nCapSel > 0 Then
                    gStep = "срез изоляции (" & capName & _
                            "): вырез поверхностью"
                    cntBefore = swPartDoc.GetFeatureCount
                    swPartDoc.InsertCutSurface capFlip, 0
                    If swPartDoc.GetFeatureCount <= cntBefore Then
                        MsgBox "Срез изоляции '" & capName & "' не " & _
                               "выполнился - торец останется " & _
                               "незакрытым.", vbExclamation
                    End If
                Else
                    MsgBox "Срез изоляции '" & capName & "': не " & _
                           "выделились тела - шаг пропущен.", vbExclamation
                End If
NEXT_CAP:
            Next capPass
            swPartDoc.ClearSelection2 True
        End If
    Next iLayer

    '--------------------------------------------------------------------------
    ' 2в. САРКОФАГ: из тел рабочего слоя (полной толщины) вычитается КЛОН
    ' изоляции. Клон расходуется вычитанием (это штатно), настоящая
    ' изоляция не участвует. InsertCombineFeature 15902: основное тело -
    ' метка 1, инструменты - метка 2 (записано рекордером 05.07.2026).
    '--------------------------------------------------------------------------
    If cloneIdx > 0 And rabIdx > 0 Then
        Dim swMainB As SldWorks.Body2
        Dim swToolB As SldWorks.Body2
        Dim kk As Long
        Dim nToolsSel As Long

        If bodyCount(rabIdx) = 1 Then
            ' одно тело рабочего слоя - вычитаем все листы клона разом
            gStep = "саркофаг: вычитание клона изоляции"
            swPartDoc.ClearSelection2 True
            Set swMainB = FirstBodyOfFeature(thkFeat(rabIdx, 1))
            nToolsSel = 0
            If Not swMainB Is Nothing Then
                If SelectBodyObj(swPartDoc, swMainB, 1, False) Then
                    For kk = 1 To bodyCount(cloneIdx)
                        Set swToolB = FirstBodyOfFeature(thkFeat(cloneIdx, kk))
                        If Not swToolB Is Nothing Then
                            If SelectBodyObj(swPartDoc, swToolB, 2, True) Then
                                nToolsSel = nToolsSel + 1
                            End If
                        End If
                    Next kk
                End If
            End If
            If nToolsSel > 0 Then
                cntBefore = swPartDoc.GetFeatureCount
                swPartDoc.FeatureManager.InsertCombineFeature _
                    15902, Nothing, Nothing
                If swPartDoc.GetFeatureCount <= cntBefore Then
                    MsgBox "Саркофаг: вычитание клона не выполнилось - " & _
                           "клон изоляции останется в модели (удалите " & _
                           "его тела вручную).", vbExclamation
                End If
            Else
                MsgBox "Саркофаг: не выделились тела для вычитания - " & _
                       "шаг пропущен, клон останется в модели.", _
                       vbExclamation
            End If
        Else
            ' несколько тел рабочего слоя - вычитаем попарно (лист k
            ' клона геометрически совпадает с листом k изоляции и
            ' лежит внутри листа k рабочего слоя - те же грани кожуха)
            For k = 1 To bodyCount(rabIdx)
                gStep = "саркофаг: вычитание клона, лист " & k
                If k > bodyCount(cloneIdx) Then Exit For
                swPartDoc.ClearSelection2 True
                Set swMainB = FirstBodyOfFeature(thkFeat(rabIdx, k))
                Set swToolB = FirstBodyOfFeature(thkFeat(cloneIdx, k))
                If swMainB Is Nothing Or swToolB Is Nothing Then
                    MsgBox "Саркофаг: не нашлись тела листа " & k & _
                           " - вычитание пропущено.", vbExclamation
                ElseIf SelectBodyObj(swPartDoc, swMainB, 1, False) And _
                       SelectBodyObj(swPartDoc, swToolB, 2, True) Then
                    cntBefore = swPartDoc.GetFeatureCount
                    swPartDoc.FeatureManager.InsertCombineFeature _
                        15902, Nothing, Nothing
                    If swPartDoc.GetFeatureCount <= cntBefore Then
                        MsgBox "Саркофаг: вычитание листа " & k & _
                               " не выполнилось - лист клона останется " & _
                               "в модели (удалите вручную).", vbExclamation
                    End If
                Else
                    MsgBox "Саркофаг: не выделились тела листа " & k & _
                           " - вычитание пропущено.", vbExclamation
                End If
            Next k
        End If
        swPartDoc.ClearSelection2 True
    End If

    '==========================================================================
    ' ФАЗА 3: "Полость" - вычитание кожуха (с рёбрами) из тел футеровки.
    ' Вызовы записаны рекордером SolidWorks 05.07.2026. Выполняется в режиме
    ' редактирования детали в контексте сборки. Только при полном успехе
    ' утолщений (ошибки фазы 2 уходят на CLEANUP_PART мимо этого блока).
    '==========================================================================
    If swShellComp Is Nothing Then
        MsgBox "Компонент кожуха не определился по выделенным граням - " & _
               "вырезы под рёбра (Полость) пропущены.", vbExclamation
    Else
        gStep = "полость: возврат в окно сборки"
        swPartDoc.ClearSelection2 True
        swApp.ActivateDoc3 assyTitle, False, _
                           swRebuildOnActivation_e.swRebuildActiveDoc, nErr

        gStep = "полость: вход в редактирование Futerovka"
        swAssyModel.ClearSelection2 True
        ok = swComp.Select4(False, Nothing, False)
        swAssy.EditPart2 True, True, nInfo

        gStep = "полость: выделение компонента кожуха"
        swAssyModel.ClearSelection2 True
        ok = swShellComp.Select4(True, Nothing, False)

        If ok Then
            gStep = "полость: InsertCavity4"
            cntBefore = swPartDoc.GetFeatureCount
            swAssyModel.InsertCavity4 0, 0, 0, True, 1, -1
            If swPartDoc.GetFeatureCount <= cntBefore Then
                MsgBox "Полость (вычитание кожуха) не создалась - " & _
                       "футеровка построена БЕЗ вырезов под рёбра.", _
                       vbExclamation
            End If
        Else
            MsgBox "Не выделился компонент кожуха - вырезы под рёбра " & _
                   "(Полость) пропущены.", vbExclamation
        End If

        gStep = "полость: выход в режим сборки"
        swAssyModel.ClearSelection2 True
        swAssy.EditAssembly
    End If

    '--------------------------------------------------------------------------
    ' Финальные имена и цвета: "Соединить" и "Полость" сбрасывают цвета,
    ' а полость ещё и режет тела на куски. Красим ВСЕ куски каждого слоя
    ' (по граням элементов утолщения), в самом конце.
    '--------------------------------------------------------------------------
    gStep = "финальные имена и цвета тел"
    For iLayer = 1 To numLayers
        If iLayer <> cloneIdx Then
            For k = 1 To bodyCount(iLayer)
                If Not thkFeat(iLayer, k) Is Nothing Then
                    ColorFeatureBodies thkFeat(iLayer, k), _
                                       nam(iLayer) & "_" & k, col(iLayer)
                End If
            Next k
        End If
    Next iLayer

CLEANUP_PART:
    gStep = "возврат в окно сборки"
    On Error Resume Next
    swPartDoc.ClearSelection2 True
    swPartDoc.ViewZoomtofit2
    swApp.ActivateDoc3 assyTitle, False, _
                       swRebuildOnActivation_e.swRebuildActiveDoc, nErr
    swAssy.EditAssembly          ' на случай ошибки в режиме редактирования
    swAssyModel.ForceRebuild3 False
    swAssyModel.GraphicsRedraw2
    On Error GoTo 0

    MsgBox "Готово. Утолщено листов: " & builtSheets & " из " & _
           totalSheets & "." & vbCrLf & vbCrLf & _
           "Отдельный файл: правой кнопкой по компоненту > " & _
           "'Сохранить деталь (во внешнем файле)'.", vbInformation
    Exit Sub

CLEANUP_ASSY:
    On Error Resume Next
    swAssyModel.ClearSelection2 True
    swAssy.EditAssembly
    On Error GoTo 0
    Exit Sub

FAIL:
    MsgBox "Ошибка VBA " & Err.Number & ": " & Err.Description & vbCrLf & _
           vbCrLf & "Шаг: " & gStep, vbCritical
    Resume CLEANUP_PART

End Sub

'------------------------------------------------------------------------------
' Запрос толщины слоя в мм. Возвращает число >= 0, либо -1 (отмена/ошибка).
' Принимает и точку, и запятую как десятичный разделитель.
'------------------------------------------------------------------------------
Private Function AskThickness(ByVal layerTitle As String, _
                              ByVal defMM As Double) As Double
    Dim s As String
    Dim v As Double

    s = InputBox("Толщина " & layerTitle & " слоя, мм." & vbCrLf & vbCrLf & _
                 "0 - этот слой не строить.", _
                 "Футеровка: толщины слоёв", CStr(defMM))
    If StrPtr(s) = 0 Then                    ' нажата "Отмена"
        AskThickness = -1
        Exit Function
    End If

    s = Trim$(Replace(s, ",", "."))
    v = Val(s)
    If s = "" Or v < 0 Or (v = 0 And Left$(s, 1) <> "0") Then
        MsgBox "Не удалось понять толщину: '" & s & "'." & vbCrLf & _
               "Нужно число в миллиметрах, например 65.", vbExclamation
        AskThickness = -1
    Else
        AskThickness = v
    End If
End Function

'------------------------------------------------------------------------------
' Число мм для имени тела: 65 -> "65", 62.5 -> "62_5"
'------------------------------------------------------------------------------
Private Function NiceMM(ByVal v As Double) As String
    Dim s As String
    s = CStr(v)
    s = Replace(s, ",", "_")
    s = Replace(s, ".", "_")
    NiceMM = s
End Function

'------------------------------------------------------------------------------
' Собирает имена УНИКАЛЬНЫХ тел, которым принадлежат грани элемента.
' Возвращает количество; имена пишет в names(layerIdx, 1..count).
'------------------------------------------------------------------------------
Private Function CollectBodyNames(ByVal swFeat As SldWorks.Feature, _
                                  ByRef names() As String, _
                                  ByVal layerIdx As Long) As Long
    Dim vFaces As Variant
    Dim swFace As SldWorks.Face2
    Dim swBody As SldWorks.Body2
    Dim cnt As Long
    Dim j As Long, known As Boolean
    Dim nm As String

    cnt = 0
    On Error Resume Next
    vFaces = swFeat.GetFaces
    On Error GoTo 0
    If IsEmpty(vFaces) Then
        CollectBodyNames = 0
        Exit Function
    End If

    Dim idxF As Long
    For idxF = LBound(vFaces) To UBound(vFaces)
        Set swFace = vFaces(idxF)
        Set swBody = swFace.GetBody
        If Not swBody Is Nothing Then
            nm = swBody.Name
            known = False
            For j = 1 To cnt
                If names(layerIdx, j) = nm Then known = True: Exit For
            Next j
            If Not known And Len(nm) > 0 And cnt < 32 Then
                cnt = cnt + 1
                names(layerIdx, cnt) = nm
            End If
        End If
    Next idxF

    CollectBodyNames = cnt
End Function

'------------------------------------------------------------------------------
' Выделяет тело поверхности с меткой Mark=1 (как требует "Придать толщину"),
' перебирая 4 способа - от подтверждённого записью макроса к обходным,
' работающим с ОБЪЕКТОМ тела (минуя поиск по имени). После каждого способа
' проверяется реальное число выделенных объектов. При полном отказе
' заполняет diag: результат каждого способа и имена всех тел детали.
'------------------------------------------------------------------------------
Private Function SelectSurfaceBody(ByVal swDoc As SldWorks.ModelDoc2, _
                                   ByVal bodyName As String, _
                                   ByRef diag As String) As Boolean
    Dim swSelMgr As SldWorks.SelectionMgr
    Dim swSelData As SldWorks.SelectData
    Dim ok As Boolean

    diag = ""
    Set swSelMgr = swDoc.SelectionManager
    On Error Resume Next
    Set swSelData = swSelMgr.CreateSelectData
    If Not swSelData Is Nothing Then swSelData.Mark = 1
    On Error GoTo 0

    ' --- Способ 1: по имени, дословно как в записанном макросе (Mark=1)
    swDoc.ClearSelection2 True
    ok = swDoc.Extension.SelectByID2(bodyName, "SURFACEBODY", _
                                     0, 0, 0, False, 1, Nothing, 0)
    If ok Or swSelMgr.GetSelectedObjectCount2(-1) > 0 Then
        SelectSurfaceBody = True
        Exit Function
    End If
    diag = diag & "1) SelectByID2 (Mark=1): False" & vbCrLf

    ' --- Ищем сам объект тела по имени среди тел поверхности детали
    Dim swPart As SldWorks.PartDoc
    Dim vBodies As Variant
    Dim swBody As SldWorks.Body2
    Dim swFound As SldWorks.Body2
    Dim iB As Long
    Dim allNames As String

    Set swPart = swDoc
    On Error Resume Next
    vBodies = swPart.GetBodies2(swBodyType_e.swSheetBody, False)
    On Error GoTo 0

    If IsEmpty(vBodies) Then
        diag = diag & "GetBodies2: в детали НЕТ тел поверхности" & vbCrLf
        SelectSurfaceBody = False
        Exit Function
    End If

    For iB = LBound(vBodies) To UBound(vBodies)
        Set swBody = vBodies(iB)
        allNames = allNames & "'" & swBody.Name & "'  "
        If swBody.Name = bodyName Then Set swFound = swBody
    Next iB
    diag = diag & "Тела в детали: " & allNames & vbCrLf

    If swFound Is Nothing Then
        diag = diag & "Тела с именем '" & bodyName & _
               "' среди них НЕТ (несовпадение имён!)" & vbCrLf
        SelectSurfaceBody = False
        Exit Function
    End If

    ' --- Способ 2: объект тела, Body2.Select2 с Mark=1
    On Error Resume Next
    swDoc.ClearSelection2 True
    ok = swFound.Select2(False, swSelData)
    On Error GoTo 0
    If ok Or swSelMgr.GetSelectedObjectCount2(-1) > 0 Then
        SelectSurfaceBody = True
        Exit Function
    End If
    diag = diag & "2) Body2.Select2 (Mark=1): False" & vbCrLf

    ' --- Способ 3: MultiSelect2 объектом тела
    Dim arrObj(0 To 0) As Object
    Dim vArr As Variant
    Dim nDone As Long
    Set arrObj(0) = swFound
    vArr = arrObj
    nDone = 0
    On Error Resume Next
    swDoc.ClearSelection2 True
    nDone = swDoc.Extension.MultiSelect2(vArr, False, swSelData)
    On Error GoTo 0
    If nDone > 0 Or swSelMgr.GetSelectedObjectCount2(-1) > 0 Then
        SelectSurfaceBody = True
        Exit Function
    End If
    diag = diag & "3) MultiSelect2: " & nDone & vbCrLf

    ' --- Способ 4: прямое добавление в список выбора
    nDone = 0
    On Error Resume Next
    swDoc.ClearSelection2 True
    nDone = swSelMgr.AddSelectionListObject(swFound, swSelData)
    On Error GoTo 0
    If nDone > 0 Or swSelMgr.GetSelectedObjectCount2(-1) > 0 Then
        SelectSurfaceBody = True
        Exit Function
    End If
    diag = diag & "4) AddSelectionListObject: " & nDone & vbCrLf

    SelectSurfaceBody = False
End Function

'------------------------------------------------------------------------------
' Выделяет тело (любого типа) ОБЪЕКТОМ с заданной меткой, перебирая
' три способа (Select2 -> MultiSelect2 -> AddSelectionListObject).
' Проверяет реальное изменение числа выделенных объектов.
'------------------------------------------------------------------------------
Private Function SelectBodyObj(ByVal swDoc As SldWorks.ModelDoc2, _
                               ByVal swBody As SldWorks.Body2, _
                               ByVal markVal As Long, _
                               ByVal appendSel As Boolean) As Boolean
    Dim swSelMgr As SldWorks.SelectionMgr
    Dim swSelData As SldWorks.SelectData
    Dim ok As Boolean
    Dim cntWas As Long

    Set swSelMgr = swDoc.SelectionManager
    cntWas = swSelMgr.GetSelectedObjectCount2(-1)

    On Error Resume Next
    Set swSelData = swSelMgr.CreateSelectData
    If Not swSelData Is Nothing Then swSelData.Mark = markVal

    ok = swBody.Select2(appendSel, swSelData)
    If Not ok And swSelMgr.GetSelectedObjectCount2(-1) <= cntWas Then
        Dim arrObj(0 To 0) As Object
        Dim vArr As Variant
        Dim nDone As Long
        Set arrObj(0) = swBody
        vArr = arrObj
        nDone = swDoc.Extension.MultiSelect2(vArr, appendSel, swSelData)
        ok = (nDone > 0)
    End If
    If Not ok And swSelMgr.GetSelectedObjectCount2(-1) <= cntWas Then
        Dim nAdd As Long
        nAdd = swSelMgr.AddSelectionListObject(swBody, swSelData)
        ok = (nAdd > 0)
    End If
    On Error GoTo 0

    SelectBodyObj = ok Or (swSelMgr.GetSelectedObjectCount2(-1) > cntWas)
End Function

'------------------------------------------------------------------------------
' Первое тело, которому принадлежат грани элемента
'------------------------------------------------------------------------------
Private Function FirstBodyOfFeature(ByVal swFeat As SldWorks.Feature) _
        As SldWorks.Body2
    Dim vFaces As Variant
    Dim swFace As SldWorks.Face2

    On Error Resume Next
    vFaces = swFeat.GetFaces
    If Not IsEmpty(vFaces) Then
        Set swFace = vFaces(0)
        Set FirstBodyOfFeature = swFace.GetBody
    End If
    On Error GoTo 0
End Function

'------------------------------------------------------------------------------
' Сигнатура подтверждена записанным макросом (SolidWorks 2019):
'   Part.FeatureManager.FeatureBossThicken(0.065, 0, 0, False, True, True, True)
' НО 5-й аргумент (bMerge, "Объединить результаты") заменён на False:
' с True слои сливались в одно тело (прогон v11). НЕ ВОЗВРАЩАТЬ True!
'------------------------------------------------------------------------------
Private Function TryThicken(ByVal swFM As Object, _
                            ByVal thickM As Double, _
                            ByVal direction As Long) As SldWorks.Feature
    Dim f As Object

    On Error Resume Next
    Err.Clear
    Set f = swFM.FeatureBossThicken(thickM, direction, 0, False, _
                                    False, True, True)
    On Error GoTo 0

    Set TryThicken = f
End Function

'------------------------------------------------------------------------------
' Красит ВСЕ тела, которым принадлежат грани элемента (после "Полости"
' тело может быть разрезано на несколько кусков - красим каждый).
' Имя получает только первый встреченный кусок.
'------------------------------------------------------------------------------
Private Sub ColorFeatureBodies(ByVal swFeat As SldWorks.Feature, _
                               ByVal baseName As String, _
                               ByVal rgbColor As Long)
    Dim vFaces As Variant
    Dim swFace As SldWorks.Face2
    Dim swB As SldWorks.Body2
    Dim idxF As Long
    Dim named As Boolean

    On Error Resume Next
    vFaces = swFeat.GetFaces
    On Error GoTo 0
    If IsEmpty(vFaces) Then Exit Sub

    named = False
    On Error Resume Next
    For idxF = LBound(vFaces) To UBound(vFaces)
        Set swFace = vFaces(idxF)
        Set swB = swFace.GetBody
        If Not swB Is Nothing Then
            If Not named Then
                swB.Name = baseName
                named = True
            End If
            ColorBody swB, rgbColor
        End If
    Next idxF
    On Error GoTo 0
End Sub

'------------------------------------------------------------------------------
Private Sub ColorBody(ByVal swBody As SldWorks.Body2, ByVal rgbColor As Long)
    Dim props(8) As Double
    props(0) = (rgbColor And &HFF&) / 255#
    props(1) = ((rgbColor \ &H100&) And &HFF&) / 255#
    props(2) = ((rgbColor \ &H10000) And &HFF&) / 255#
    props(3) = 1:   props(4) = 1:  props(5) = 0.5
    props(6) = 0.3: props(7) = 0:  props(8) = 0
    swBody.MaterialPropertyValues2 = props
End Sub
