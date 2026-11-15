(define (problem rebuild-13)
  (:domain blocks-world)
  (:objects a b c d e - block)
  (:init
    (clear a) (clear b) (clear c) (clear d) (hand-empty) (on c e) (on-table a) (on-table b) (on-table d) (on-table e))
  (:goal (and (on a e) (on c b) (on d c) (on e d) (on-table b)))
)
